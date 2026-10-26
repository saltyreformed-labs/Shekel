"""Account route tests: the cash detail hero's click-to-edit, and the anchor kind gate."""

from decimal import Decimal

from app.extensions import db
from app.models.account import Account, AccountAnchorHistory
from app.utils.dates import display_today
from tests._test_helpers import record_paydays_across_a_hole, rhythm_of
from app.models.ref import AccountType
from app.services import account_service, cash_ledger

from ._helpers import _create_other_user_account


# ── Cash Detail Click-to-Edit Hero (S8 / D14 port) ────────────────


class TestCashDetailClickToEditHero:
    """S8 / D14: the cash detail hero doubles as the anchor true-up control.

    Cash previously had NO on-page anchor recording (P-DT8).  The hero
    reuses the shared anchor editor (accounts.anchor_form /
    accounts.true_up / anchor_service) via a new ``revert=cash``
    surface: ``accounts.cash_balance_hero`` is the Cancel / Escape
    revert target (it was a 409-conflict target too, until ruling R-EN
    deleted the 409 at plan step X-f1c3c), and a save fires
    ``balanceChanged`` so the page's ``#cash-band-region`` re-fetches
    ``accounts.cash_band`` -- the hero, horizon chips, interest chip, and
    chart all recompute from the new anchor together.

    ``test_cash_conflict_cell_retry_opener_carries_revert`` was DELETED here at
    plan step X-f1c3c, the fourth of the four conflict-cell cases the ruling
    removed (the other three were in ``TestAnchorTemplatesEmitVersionPin``,
    which records them).  It asserted that a 409 conflict cell's retry opener
    carried ``revert=cash``; there is no 409.  What the token still routes --
    Cancel, Escape and the success re-render -- is graded by
    ``test_anchor_form_cash_revert_targets_cash_hero`` and
    ``test_cash_anchor_form_patch_url_threads_revert`` below, so no live
    behaviour lost its coverage.
    """

    def test_page_hero_is_click_to_edit_inside_band_region(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The detail page wires the band region and the editor opener.

        The hero opens the shared editor scoped to the cash surface, and
        the band region re-fetches accounts.cash_band on balanceChanged
        (the L6 oracle's data-current-balance hook must survive on the
        hero cell).
        """
        with app.app_context():
            acct_id = seed_user["account"].id
            response = auth_client.get(f"/accounts/{acct_id}/details")
            assert response.status_code == 200
            body = response.data.decode()
            assert 'id="cash-band-region"' in body
            assert f'hx-get="/accounts/{acct_id}/details/band"' in body
            assert 'hx-trigger="balanceChanged from:body"' in body
            assert 'id="cash-balance-hero"' in body
            assert (
                f'hx-get="/accounts/{acct_id}/anchor-form?revert=cash"'
                in body
            )
            assert "data-current-balance=" in body

    def test_cash_balance_hero_renders_display_cell(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """GET (HX) returns the resolver balance and the editor opener.

        The seed account is anchored at $1000.00 with no transactions,
        so the resolver current-period balance the hero shows is exactly
        1,000.00, and the L6 oracle hook carries the raw Decimal.
        """
        with app.app_context():
            acct_id = seed_user["account"].id
            response = auth_client.get(
                f"/accounts/{acct_id}/details/balance-hero",
                headers={"HX-Request": "true"},
            )
            assert response.status_code == 200
            body = response.data.decode()
            assert "$1,000.00" in body
            assert 'data-current-balance="1000.00"' in body
            assert 'id="cash-balance-hero"' in body
            assert (
                f'hx-get="/accounts/{acct_id}/anchor-form?revert=cash"'
                in body
            )

    def test_cash_balance_hero_redirects_without_htmx(
        self, app, auth_client, seed_user,
    ):
        """GET without HX-Request redirects to the detail page."""
        with app.app_context():
            acct_id = seed_user["account"].id
            response = auth_client.get(
                f"/accounts/{acct_id}/details/balance-hero",
            )
            assert response.status_code == 302
            assert f"/accounts/{acct_id}/details" in response.headers.get(
                "Location", "",
            )

    def test_cash_balance_hero_idor(self, app, auth_client, seed_user):
        """GET another user's cash hero returns 404 and leaks nothing."""
        with app.app_context():
            other = _create_other_user_account()
            response = auth_client.get(
                f"/accounts/{other['account'].id}/details/balance-hero",
                headers={"HX-Request": "true"},
            )
            assert response.status_code == 404

    def test_cash_band_renders_full_band(
        self, app, auth_client, seed_user,
    ):
        """GET (HX) returns the whole band: hero, caption, chips, chart.

        The balanceChanged refresh target must carry every
        anchor-derived figure so no band surface can disagree with a
        fresh anchor.  Periods are generated with ``num_periods=10``
        starting today so the anchor sits on period 0 and the 3-month
        horizon (period_index 6) is reachable -- ``seed_periods_today``
        would anchor today on period 4 and ``4 + 6 = 10`` exceeds its
        window, omitting the chip (the ``TestCheckingDetail`` pattern).
        Hand arithmetic: anchor 2500.00, no transactions -> the hero
        and every horizon chip read a flat $2,500.
        """
        with app.app_context():
            # The CALL is the setup: it creates the ten periods the
            # band's horizon chips read across.
            record_paydays_across_a_hole(
                user_id=seed_user["user"].id,
                first_payday=display_today(),
                num_periods=10, rhythm=rhythm_of(14),
            )
            checking_type = db.session.query(AccountType).filter_by(
                name="Checking",
            ).one()
            account = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=checking_type.id,
                    name="Band Checking",
                    anchor_balance=Decimal("2500.00"),
                ),
            )
            db.session.flush()
            db.session.commit()

            response = auth_client.get(
                f"/accounts/{account.id}/details/band",
                headers={"HX-Request": "true"},
            )
            assert response.status_code == 200
            body = response.data.decode()
            assert 'id="cash-balance-hero"' in body
            assert "$2,500.00" in body
            assert "current period" in body
            assert "In 3 months" in body
            assert 'id="account-detail-chart-canvas"' in body

    def test_cash_band_redirects_without_htmx(
        self, app, auth_client, seed_user,
    ):
        """GET without HX-Request redirects to the detail page."""
        with app.app_context():
            acct_id = seed_user["account"].id
            response = auth_client.get(f"/accounts/{acct_id}/details/band")
            assert response.status_code == 302
            assert f"/accounts/{acct_id}/details" in response.headers.get(
                "Location", "",
            )

    def test_cash_band_idor(self, app, auth_client, seed_user):
        """GET another user's band returns 404 and leaks nothing."""
        with app.app_context():
            other = _create_other_user_account()
            response = auth_client.get(
                f"/accounts/{other['account'].id}/details/band",
                headers={"HX-Request": "true"},
            )
            assert response.status_code == 404

    def test_anchor_form_cash_revert_targets_cash_hero(
        self, app, auth_client, seed_user,
    ):
        """With ?revert=cash, the editor reverts to the cash hero cell.

        Opened from the cash detail hero, Cancel / Escape must restore
        THAT cell (``accounts.cash_balance_hero``) rather than the grid
        display cell -- the cash analog of the ``revert=accounts``
        round-trip.
        """
        with app.app_context():
            acct_id = seed_user["account"].id
            response = auth_client.get(
                f"/accounts/{acct_id}/anchor-form?revert=cash",
            )
            assert response.status_code == 200
            body = response.data.decode()
            cell_url = f"/accounts/{acct_id}/details/balance-hero"
            assert f'hx-get="{cell_url}"' in body
            assert f'data-revert-url="{cell_url}"' in body
            assert f"/accounts/{acct_id}/anchor-display" not in body

    def test_cash_anchor_form_patch_url_threads_revert(
        self, app, auth_client, seed_user,
    ):
        """The cash edit form's hx-patch threads ``revert=cash``.

        So a 409 conflict response can re-render the conflict cell with
        the cash retry target rather than stranding the hero on the grid
        display cell.
        """
        with app.app_context():
            acct_id = seed_user["account"].id
            response = auth_client.get(
                f"/accounts/{acct_id}/anchor-form?revert=cash",
            )
            assert response.status_code == 200
            body = response.data.decode()
            assert (
                f'hx-patch="/accounts/{acct_id}/true-up?revert=cash"'
                in body
            )


class TestAnchorKindGate:
    """The D4 / A1 amortizing-kind gate on every cash-anchor write door.

    Finding B-15: ``PATCH /accounts/<id>/true-up`` (and the full-form
    edit) wrote ``accounts.current_anchor_balance`` for an AMORTIZING
    loan -- a second, stored, never-reconciled loan balance, which the
    grid then rendered (the real Mortgage's column was set to $1.00
    with an HTTP 200).  A loan's balance is ledger-derived and is
    asserted through the loan page's own true-up; every cash-anchor
    door now refuses the kind.
    """

    @staticmethod
    def _loan(seed_user):
        """Create a fully configured loan through the shared builder."""
        from tests._test_helpers import create_loan_account  # pylint: disable=import-outside-toplevel

        return create_loan_account(
            seed_user, db.session, name="Gate Test Loan",
            principal=Decimal("10000.00"), rate=Decimal("0.05000"),
        )

    def test_true_up_refuses_amortizing_loan(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """PATCH true-up on a loan: 422, and NOTHING is written.

        Asserts all three no-write invariants: the cash anchor column is
        unchanged, no ``AccountAnchorHistory`` row was appended, and no
        ``LoanAnchorEvent`` was created (the refusal must not leak into
        the loan's REAL true-up path either).
        """
        from app.models.loan_anchor_event import LoanAnchorEvent  # pylint: disable=import-outside-toplevel

        with app.app_context():
            loan = self._loan(seed_user)
            column_before = cash_ledger.resolve_anchor(loan).balance
            history_before = (
                db.session.query(AccountAnchorHistory)
                .filter_by(account_id=loan.id).count()
            )
            events_before = (
                db.session.query(LoanAnchorEvent)
                .filter_by(account_id=loan.id).count()
            )

            response = auth_client.patch(
                f"/accounts/{loan.id}/true-up",
                data={"anchor_balance": "1.00"},
            )

            assert response.status_code == 422
            assert b"not a cash anchor" in response.data

            refreshed = db.session.get(Account, loan.id)
            assert cash_ledger.resolve_anchor(refreshed).balance == column_before
            assert (
                db.session.query(AccountAnchorHistory)
                .filter_by(account_id=loan.id).count()
            ) == history_before
            assert (
                db.session.query(LoanAnchorEvent)
                .filter_by(account_id=loan.id).count()
            ) == events_before

    def test_the_kind_refusal_is_a_fragment_the_submitting_form_can_render(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The kind refusal is DESIGNED, so the user actually sees it.

        **Plan step X-f1e3, finding N-199.**  This arm answered a raw string
        body, and ``base.html``'s htmx config leaves 4xx non-swapping, so the
        refusal rendered NOTHING and the form simply sat there -- the exact
        defect X-f1c4c converted the door's other arm to prevent.

        It was left raw on the argument that the arm answers a forged request:
        ``anchor_form`` refuses to OPEN the editor for a loan, so no user could
        reach the PATCH.  **That argument is false, and the test below proves
        the path.**  An account's kind is editable, and a boundary-crossing
        re-type is permitted while the account carries no ledger postings, so a
        form opened on a cash account can be submitted after that account has
        become a loan.

        Graded on the MARKER HEADER, not on the status: the header is what the
        global ``htmx:beforeSwap`` listener reads to swap a 4xx at all, so a
        fragment without it is invisible however well it renders.  The 422 is
        asserted too -- a designed fragment does not need a 2xx, and flattening
        this onto the input-shaped 400 would tell a non-htmx client the payload
        was malformed when it was the entity that could not be processed.
        """
        with app.app_context():
            loan = self._loan(seed_user)

            response = auth_client.patch(
                f"/accounts/{loan.id}/true-up",
                data={"anchor_balance": "1.00"},
            )

            assert response.status_code == 422
            assert response.headers.get("Shekel-Designed-Fragment") == "1", (
                "without the marker header htmx leaves this 4xx non-swapping, "
                "so the refusal is invisible and the form sits there"
            )
            body = response.data.decode()
            assert "not a cash anchor" in body
            assert 'role="alert"' in body
            # **It renders the read-only DISPLAY cell, not the editor.**  An
            # adversarial review caught the first version of this fix
            # re-rendering the editor: a live input and a Save button whose
            # PATCH is guaranteed to be refused again -- the dead-end
            # affordance this module's own ``anchor_form`` docstring forbids,
            # twelve lines below the arm that was offering it.  There is
            # nothing here to resubmit, so the response must not look like
            # there is.
            assert 'name="anchor_balance"' not in body, (
                "a refusal that can never be satisfied must not re-offer the "
                "form that cannot satisfy it"
            )
            assert 'id="anchor-display"' not in body
            assert "hx-get" not in body

    def test_a_cash_account_can_become_a_loan_under_an_open_editor(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The kind refusal's user path exists -- it is not a forged request.

        **The measurement behind the docstring correction in
        ``_true_up_request_gates``** (finding N-199).  This test does not
        assert on the refusal; it asserts the STATE the refusal answers is
        reachable through ordinary routes, which is what makes a designed
        fragment worth rendering.

        The sequence is two browser tabs: open the anchor editor on a cash
        account, re-type that account to an amortizing kind, then submit the
        still-open form.  The re-type is permitted here because the account
        carries no ledger postings -- a ``$0.00`` opening emits no legs, so
        ``_validate_account_type_change``'s posting guard does not bite.
        """
        with app.app_context():
            # A SAVINGS account whose opening asserts $0.00: a zero delta
            # emits no legs, so its ledger is empty and the posting guard in
            # ``_validate_account_type_change`` does not bite.  Built through
            # the service, the shape ``TestTypeChangeBoundaryGuard`` uses.
            savings_type = (
                db.session.query(AccountType)
                .filter_by(name="Savings", user_id=None).one()
            )
            account = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=savings_type.id,
                    name="Opened At Zero",
                    anchor_balance=Decimal("0.00"),
                ),
            )
            db.session.commit()
            account_id = account.id

            # Tab A opens the editor: the account is cash, so it opens.
            assert auth_client.get(
                f"/accounts/{account_id}/anchor-form",
            ).status_code == 200

            # Tab B re-types it across the amortizing boundary.
            mortgage_type = (
                db.session.query(AccountType)
                .filter_by(name="Mortgage", user_id=None).one()
            )
            assert mortgage_type.has_amortization, (
                "the fixture type is not amortizing, so this test would not "
                "cross the boundary it exists to cross"
            )
            auth_client.post(
                f"/accounts/{account_id}",
                data={"account_type_id": str(mortgage_type.id)},
                follow_redirects=True,
            )
            db.session.expire_all()
            assert db.session.get(
                Account, account_id,
            ).account_type.has_amortization, (
                "the re-type was refused, so this test is no longer proving "
                "the kind refusal is reachable -- re-check "
                "_validate_account_type_change's posting guard"
            )

            # Tab A submits the form it opened while the account was cash.
            submitted = auth_client.patch(
                f"/accounts/{account_id}/true-up",
                data={"anchor_balance": "1.00"},
            )
            assert submitted.status_code == 422
            assert submitted.headers.get("Shekel-Designed-Fragment") == "1"

    def test_anchor_form_refuses_amortizing_loan(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """GET anchor-form for a loan: 422 -- the editor never even opens.

        Designed since plan step X-f1e3, for the same reason as its PATCH
        twin: a raw 4xx is left non-swapping by ``base.html``, so the click
        produced NOTHING at all -- and unlike the PATCH there was not even a
        form sitting there to explain it.
        """
        with app.app_context():
            loan = self._loan(seed_user)

            response = auth_client.get(f"/accounts/{loan.id}/anchor-form")

            assert response.status_code == 422
            assert b"not a cash anchor" in response.data
            assert response.headers.get("Shekel-Designed-Fragment") == "1"

    def test_a_loan_balance_cell_offers_no_click_to_edit(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The refused affordance is DELETED, not decorated with a refusal.

        **Plan step X-f1e3's root fix for finding N-199's second half.**  Both
        anchor doors refuse an amortizing account, and the shared display
        partial offered the click anyway on every surface that includes it, so
        an ordinary click reached a refusal the page could not render.  A
        control that cannot succeed is not offered: the cell renders read-only,
        which is the rule ``savings/_cockpit_balance.html`` already followed
        and the other four surfaces did not.

        Graded on the ABSENCE of the opener, not on the presence of the
        figure: a mutant that kept ``hx-get`` while adding a title attribute
        would still be a dead click.
        """
        with app.app_context():
            loan = self._loan(seed_user)

            body = auth_client.get(
                f"/accounts/{loan.id}/anchor-display",
            ).data.decode()

            assert "anchor-balance-display" in body, (
                "the loan's balance must still be SHOWN -- this step removes "
                "the edit affordance, not the figure"
            )
            assert 'id="anchor-display"' not in body
            assert "hx-get" not in body
            assert "data-keyboard-activate" not in body
            assert 'role="button"' not in body

    def test_a_cash_balance_cell_still_offers_click_to_edit(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The negative control for the read-only branch, on a CASH account.

        Without this, deleting the affordance for EVERY account would pass the
        test above -- and would silently remove the one-click true-up habit
        this whole arc is built around.
        """
        with app.app_context():
            body = auth_client.get(
                f"/accounts/{seed_user['account'].id}/anchor-display",
            ).data.decode()

            assert 'id="anchor-display"' in body
            assert "hx-get" in body
            assert 'role="button"' in body

    def test_update_form_cannot_assert_a_loan_balance_at_all(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A loan anchor through the edit door is unreachable, not refused.

        This door used to need its OWN copy of the amortizing-kind gate: it
        reached ``stage_anchor_true_up`` without passing
        ``apply_anchor_true_up``'s :class:`AmortizingAccountAnchorError`, so
        without the copy a forged POST could write a cash assertion onto a loan
        -- finding B-15, where the real Mortgage's balance was set to $1.00 with
        an HTTP 200 while the ledger said $177,277.97.

        Plan step X-f1e deleted the door, so the gate is not needed here and was
        deleted with it.  The predecessor of this test asserted the REFUSAL
        ("not a cash anchor" in the response); refusing is now the wrong thing
        to assert, because there is nothing to refuse -- the schema discards the
        field.

        **What separates the two worlds is whether the REST of the edit
        applied**, and that is the assertion this test turns on.  "200, balance
        unmoved, nothing appended" was true under the old gate as well: it
        flashed and redirected, which ``follow_redirects`` renders as a 200, and
        it wrote nothing -- including the rename the user also asked for.  So
        the rename is CHANGED here and asserted: the old door threw the whole
        edit away, the new one ignores one field and applies the rest.  Without
        that line this test passes against a revert, which a neutral review
        measured before it was added.
        """
        with app.app_context():
            loan = self._loan(seed_user)
            loan_id = loan.id
            balance_before = cash_ledger.resolve_anchor(loan).balance
            assertions_before = (
                db.session.query(AccountAnchorHistory)
                .filter_by(account_id=loan_id).count()
            )

            response = auth_client.post(f"/accounts/{loan_id}", data={
                "name": "Renamed Gate Loan Two",
                "account_type_id": loan.account_type_id,
                "anchor_balance": "1.00",
            }, follow_redirects=True)

            assert response.status_code == 200
            db.session.expire_all()
            refreshed = db.session.get(Account, loan_id)
            # The loan's balance is untouched -- finding B-15's $1.00.
            assert cash_ledger.resolve_anchor(refreshed).balance == balance_before
            assert (
                db.session.query(AccountAnchorHistory)
                .filter_by(account_id=loan_id).count()
            ) == assertions_before
            # ...and the edit this door DOES own still landed, which the old
            # gate's flash-and-redirect refusal would have discarded.
            assert refreshed.name == "Renamed Gate Loan Two"

    def test_update_form_allows_loan_edit_with_unchanged_anchor(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A loan rename with the form's unchanged anchor echo succeeds.

        The edit form used to round-trip the asserted balance on every submit,
        and an unchanged echo is not an assertion, so gating it would have
        broken every ordinary loan edit.  Plan step X-f1e removed the field, so
        a stale client still sending it must not break the edit either -- which
        is what this now pins.
        """
        with app.app_context():
            loan = self._loan(seed_user)

            response = auth_client.post(f"/accounts/{loan.id}", data={
                "name": "Renamed Gate Loan",
                "account_type_id": loan.account_type_id,
                "anchor_balance": str(cash_ledger.resolve_anchor(loan).balance),
            }, follow_redirects=True)

            assert response.status_code == 200
            assert b"not a cash anchor" not in response.data

            refreshed = db.session.get(Account, loan.id)
            assert refreshed.name == "Renamed Gate Loan"
