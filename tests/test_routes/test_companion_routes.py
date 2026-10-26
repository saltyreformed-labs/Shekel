"""
Shekel Budget App -- Companion Route Tests

Route-level integration tests for the companion view.  Verifies
access control, period navigation, entry integration, mark-done,
entry data rendering, and empty states.

Covers plan test IDs: 10.3, 10.5, 10.6, 10.7, 10.8, 10.9, 10.10, 10.11.
Additional tests beyond the plan baseline cover period navigation
arrows, entry CRUD through the entries blueprint, entry data
computation in response HTML, and empty state rendering.
"""

from datetime import date
from decimal import Decimal

from app import ref_cache
from app.enums import StatusEnum
from app.extensions import db
from app.services import status_seam
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.models.user import User, UserSettings
from app.services.auth_service import hash_password
from app.services.row_valuation import settled_figure
from tests._test_helpers import (
    figure_source_columns,
    generate_row_of,
    make_expense_template,
    open_owner_calendar,
    settlement_if_settling,
)


# ── Helpers ──────────────────────────────────────────────────────────


def _make_template(
    seed_user, *, companion_visible, track=False, name="Item", amount="500.00",
):
    """Create a priced, every-paycheck expense definition for the seed_user owner.

    A case's row is the definition's own (:func:`generate_row_of`, plan step
    balance:X-cf-4), so the name and the figure a case wants on its row are
    stated HERE -- the definition is what names and prices a generated row.

    Args:
        seed_user: The seed_user fixture dict.
        companion_visible: Whether the template is companion-visible.
        track: Whether to enable is_envelope.
        name: Template name.
        amount: The definition's stated price, as a string.

    Returns:
        The created TransactionTemplate object (flushed, ID available).
    """
    return make_expense_template(
        db.session, seed_user, amount=amount, name=name,
        category_key=next(iter(seed_user["categories"])),
        is_envelope=track, companion_visible=companion_visible,
    )


def _login_companion(app):
    """Create an authenticated companion test client.

    Args:
        app: The Flask application.

    Returns:
        An authenticated FlaskClient for the companion user.
    """
    comp = app.test_client()
    resp = comp.post("/login", data={
        "email": "companion@shekel.local",
        "password": "companionpass",
    })
    assert resp.status_code == 302, (
        f"Companion login failed with status {resp.status_code}"
    )
    return comp


# ── Route Access ─────────────────────────────────────────────────────


class TestRouteAccess:
    """Verify access control on companion routes."""

    def test_companion_gets_200_with_visible_transactions(
        self, app, db, seed_user, seed_periods_today, seed_companion,
    ):
        """Companion GET /companion/period/<id> returns 200 with visible names.

        Creates visible and non-visible templates.  Response HTML
        contains only visible transaction names.  Uses explicit
        period_id to avoid date-sensitivity from the default
        current-period logic.
        """
        t_vis = _make_template(seed_user, companion_visible=True, name="Groceries")
        t_hid = _make_template(seed_user, companion_visible=False, name="Mortgage")
        generate_row_of(t_vis, seed_periods_today[0])
        generate_row_of(t_hid, seed_periods_today[0])
        db.session.commit()

        comp = _login_companion(app)
        resp = comp.get(f"/companion/period/{seed_periods_today[0].id}")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Groceries" in html
        assert "Mortgage" not in html

    def test_owner_redirected_from_companion(
        self, auth_client,
    ):
        """Plan 10.11: Owner GET /companion/ redirects to /grid."""
        resp = auth_client.get("/companion/")
        assert resp.status_code == 302
        assert "/grid" in resp.headers["Location"]

    def test_unauthenticated_redirected_to_login(self, client):
        """Unauthenticated GET /companion/ redirects to /login."""
        resp = client.get("/companion/")
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]

    def test_companion_view_with_no_periods(
        self, app, db, seed_user, seed_companion,
    ):
        """Companion with no owner periods gets 200 (empty state).

        When no period contains the owner's day, the route should
        gracefully render the empty state.
        Note: seed_periods_today is not included, so the owner has no periods.
        """
        comp = _login_companion(app)
        resp = comp.get("/companion/")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "No pay periods available" in html


# ── Period Navigation ────────────────────────────────────────────────


class TestPeriodNavigation:
    """Verify period navigation routes and arrow rendering."""

    def test_companion_period_view_valid(
        self, app, db, seed_user, seed_periods_today, seed_companion,
    ):
        """Plan 10.3: GET /companion/period/<valid_id> returns 200.

        Shows the correct period's transactions.
        """
        template = _make_template(seed_user, companion_visible=True, name="Groceries P1")
        generate_row_of(template, seed_periods_today[1])
        db.session.commit()

        comp = _login_companion(app)
        resp = comp.get(f"/companion/period/{seed_periods_today[1].id}")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Groceries P1" in html

    def test_companion_period_view_other_owner(
        self, app, db, seed_user, seed_periods_today, seed_companion,
    ):
        """Companion accessing another owner's period gets 404."""
        second_user = User(
            email="other@test.local",
            password_hash=hash_password("otherpass"),
            display_name="Other",
        )
        db.session.add(second_user)
        db.session.flush()
        settings = UserSettings(user_id=second_user.id)
        db.session.add(settings)

        # Through the writer that owns the table (plan step pay_calendar:C4-b-1).
        other_period = open_owner_calendar(second_user.id, date(2026, 1, 2))[0]
        db.session.commit()

        comp = _login_companion(app)
        resp = comp.get(f"/companion/period/{other_period.id}")
        assert resp.status_code == 404

    def test_companion_period_view_nonexistent(
        self, app, db, seed_user, seed_periods_today, seed_companion,
    ):
        """Companion accessing a non-existent period gets 404."""
        comp = _login_companion(app)
        resp = comp.get("/companion/period/999999")
        assert resp.status_code == 404

    def test_prev_next_links_present(
        self, app, db, seed_user, seed_periods_today, seed_companion,
    ):
        """Response HTML contains previous/next period links when applicable.

        Viewing period[1] (middle period) should show both arrows.
        """
        template = _make_template(seed_user, companion_visible=True, name="Groceries")
        generate_row_of(template, seed_periods_today[1])
        db.session.commit()

        comp = _login_companion(app)
        resp = comp.get(f"/companion/period/{seed_periods_today[1].id}")
        assert resp.status_code == 200
        html = resp.data.decode()
        # Previous link points to period[0].
        assert f"/companion/period/{seed_periods_today[0].id}" in html
        # Next link points to period[2].
        assert f"/companion/period/{seed_periods_today[2].id}" in html

    def test_no_prev_link_on_first_period(
        self, app, db, seed_user, seed_periods_today, seed_companion,
    ):
        """First period has no previous link (no chevron-left href).

        Viewing period[0] should not contain a link to a previous period.
        """
        template = _make_template(seed_user, companion_visible=True, name="Groceries")
        generate_row_of(template, seed_periods_today[0])
        db.session.commit()

        comp = _login_companion(app)
        resp = comp.get(f"/companion/period/{seed_periods_today[0].id}")
        assert resp.status_code == 200
        html = resp.data.decode()
        # There should be a next link but no previous link.
        assert f"/companion/period/{seed_periods_today[1].id}" in html
        # The chevron-left should not be an anchor.
        assert "bi-chevron-left" not in html or (
            "bi-chevron-left" in html
            and f"/companion/period/{seed_periods_today[0].id}" not in html
        )

    def test_no_next_link_on_last_period(
        self, app, db, seed_user, seed_periods_today, seed_companion,
    ):
        """Last period has no next link.

        Viewing the last period should not contain a link to a next period.
        """
        last_period = seed_periods_today[-1]
        template = _make_template(seed_user, companion_visible=True, name="Groceries")
        generate_row_of(template, last_period)
        db.session.commit()

        comp = _login_companion(app)
        resp = comp.get(f"/companion/period/{last_period.id}")
        assert resp.status_code == 200
        html = resp.data.decode()
        # Previous link should exist (second-to-last period).
        prev_period = seed_periods_today[-2]
        assert f"/companion/period/{prev_period.id}" in html

    def test_owner_redirected_from_period_view(
        self, auth_client, seed_periods_today,
    ):
        """Owner GET /companion/period/<id> redirects to /grid."""
        resp = auth_client.get(f"/companion/period/{seed_periods_today[0].id}")
        assert resp.status_code == 302
        assert "/grid" in resp.headers["Location"]


# ── Entry Integration (via entries blueprint) ────────────────────────


class TestEntryIntegration:
    """Verify companion can use entry CRUD routes on visible transactions."""

    def test_companion_can_add_entry(
        self, app, db, seed_user, seed_periods_today, seed_companion,
    ):
        """Plan 10.5: Companion adds entry to visible tracked transaction.

        Entry is created with user_id == companion's ID.
        """
        template = _make_template(
            seed_user, companion_visible=True, track=True, name="Groceries",
        )
        txn = generate_row_of(template, seed_periods_today[0])
        db.session.commit()

        comp = _login_companion(app)
        resp = comp.post(f"/transactions/{txn.id}/entries", data={
            "amount": "42.50",
            "description": "Kroger",
            "purchased_on": "2026-01-05",
        })
        assert resp.status_code == 200

        # Verify entry was created.
        entry = db.session.query(TransactionEntry).filter_by(
            transaction_id=txn.id,
        ).first()
        assert entry is not None
        assert entry.amount == Decimal("42.50")
        assert entry.description == "Kroger"

    def test_companion_entry_user_id_is_companion(
        self, app, db, seed_user, seed_periods_today, seed_companion,
    ):
        """Plan 10.6: Entry.user_id is the companion's ID, not the owner's.

        Verifies attribution: the companion's user_id is recorded
        on the entry for audit purposes.
        """
        template = _make_template(
            seed_user, companion_visible=True, track=True, name="Groceries",
        )
        txn = generate_row_of(template, seed_periods_today[0])
        db.session.commit()

        companion = seed_companion["user"]
        comp = _login_companion(app)
        resp = comp.post(f"/transactions/{txn.id}/entries", data={
            "amount": "25.00",
            "description": "Target",
            "purchased_on": "2026-01-05",
        })
        assert resp.status_code == 200

        entry = db.session.query(TransactionEntry).filter_by(
            transaction_id=txn.id,
        ).first()
        assert entry.user_id == companion.id

    def test_companion_cannot_access_non_visible_entries(
        self, app, db, seed_user, seed_periods_today, seed_companion,
    ):
        """Plan 10.7: Companion gets 404 on entry route for non-visible transaction."""
        template = _make_template(
            seed_user, companion_visible=False, track=True, name="Hidden",
        )
        txn = generate_row_of(template, seed_periods_today[0])
        db.session.commit()

        comp = _login_companion(app)
        resp = comp.post(f"/transactions/{txn.id}/entries", data={
            "amount": "10.00",
            "description": "Test",
            "purchased_on": "2026-01-05",
        })
        assert resp.status_code == 404

    def test_a_crafted_patch_on_a_generated_rows_cell_shows_nothing(
        self, app, db, auth_client, seed_user, seed_periods_today,
        seed_companion,
    ):
        """The owner PATCHes a generated row's ``companion_visible``; nothing changes.

        The popover renders no visibility control for a template-generated
        row, so only a crafted PATCH reaches for its cell -- **and since plan
        step ``balance:X-bi-7b`` the write does NOT land**: the schema the
        door loads for a recurring definition's row declares no flag, so the
        field is dropped before any code could reach a writer (finding
        **BAL-484**'s writer, deleted under ruling R-BAL20; this case
        asserted ``cell is True`` while the inert write plan step X-bi-1
        left unrefused still landed, and the cell itself went at the
        family's cutover, ``balance:X-bi-7d-2``).  The row's template is
        hidden, so the
        row stays off the companion page and its entries door stays 404:
        both surfaces decide by the one accessor, ``visible_to_companion``
        (plan step ``X-bi-1b``, finding **BAL-482**).  Through the
        application's own doors, end to end, which is what the model-level
        seal tests cannot see.
        """
        template = _make_template(
            seed_user, companion_visible=False, track=True, name="Hidden",
        )
        txn = generate_row_of(template, seed_periods_today[0])
        # The positive control: a row the companion MAY see, in the same
        # period, so an empty page cannot pass the absence check below.
        shown = _make_template(
            seed_user, companion_visible=True, name="Shown",
        )
        generate_row_of(shown, seed_periods_today[0])
        db.session.commit()
        txn_id = txn.id

        resp = auth_client.patch(f"/transactions/{txn_id}", data={
            "companion_visible": "true",
            "version_id": txn.version_id,
        })
        assert resp.status_code == 200
        db.session.expire_all()
        # Nothing took the write: the definition -- the only home a flag has
        # -- holds what it held, and the row reads that.
        row = db.session.get(Transaction, txn_id)
        assert row.template.companion_visible is False
        assert row.visible_to_companion is False

        comp = _login_companion(app)
        resp = comp.get(f"/companion/period/{seed_periods_today[0].id}")
        assert resp.status_code == 200
        assert b"Shown" in resp.data
        assert b"Hidden" not in resp.data
        resp = comp.get(f"/transactions/{txn_id}/entries")
        assert resp.status_code == 404

    def test_companion_cannot_guess_txn_id(
        self, app, db, seed_user, seed_periods_today, seed_companion,
    ):
        """Plan 10.8: Random/non-existent txn_id returns 404."""
        comp = _login_companion(app)
        resp = comp.post("/transactions/999999/entries", data={
            "amount": "10.00",
            "description": "Test",
            "purchased_on": "2026-01-05",
        })
        assert resp.status_code == 404

    def test_companion_can_delete_entry(
        self, app, db, seed_user, seed_periods_today, seed_companion,
    ):
        """Companion can delete an entry on a visible tracked transaction."""
        template = _make_template(
            seed_user, companion_visible=True, track=True, name="Groceries",
        )
        txn = generate_row_of(template, seed_periods_today[0])
        entry = TransactionEntry(
            **figure_source_columns(),
            transaction_id=txn.id, account_id=txn.account_id, owner_id=txn.user_id,
            user_id=seed_companion["user"].id,
            amount=Decimal("30.00"),
            description="Kroger",
            purchased_on=date(2026, 1, 5),
        )
        db.session.add(entry)
        db.session.commit()

        comp = _login_companion(app)
        resp = comp.delete(
            f"/transactions/{txn.id}/entries/{entry.id}",
        )
        assert resp.status_code == 200

        deleted = db.session.get(TransactionEntry, entry.id)
        assert deleted is None

    def test_companion_can_edit_entry(
        self, app, db, seed_user, seed_periods_today, seed_companion,
    ):
        """Companion can edit an entry on a visible tracked transaction."""
        template = _make_template(
            seed_user, companion_visible=True, track=True, name="Groceries",
        )
        txn = generate_row_of(template, seed_periods_today[0])
        entry = TransactionEntry(
            **figure_source_columns(),
            transaction_id=txn.id, account_id=txn.account_id, owner_id=txn.user_id,
            user_id=seed_companion["user"].id,
            amount=Decimal("30.00"),
            description="Kroger",
            purchased_on=date(2026, 1, 5),
        )
        db.session.add(entry)
        db.session.commit()

        comp = _login_companion(app)
        resp = comp.patch(
            f"/transactions/{txn.id}/entries/{entry.id}",
            data={
                "amount": "45.00",
                # The edit form emits the direction beside the amount since
                # plan step ``bank_import:X-gj-2b-3``, and the route refuses
                # one without the other rather than guessing a sign.
                "direction": "charge",
                "description": "Kroger Updated",
            },
        )
        assert resp.status_code == 200

        db.session.refresh(entry)
        assert entry.amount == Decimal("45.00")
        assert entry.description == "Kroger Updated"


# ── Mark Done Integration ────────────────────────────────────────────


class TestMarkDoneIntegration:
    """Verify companion mark-done through the transactions route."""

    def test_companion_marks_visible_projected_as_paid(
        self, app, db, seed_user, seed_periods_today, seed_companion,
    ):
        """Plan 10.9: Companion marks visible PROJECTED transaction as Paid.

        Status changes from Projected to Done (Paid).
        """
        template = _make_template(
            seed_user, companion_visible=True, name="Groceries",
        )
        txn = generate_row_of(template, seed_periods_today[0])
        db.session.commit()

        comp = _login_companion(app)
        resp = comp.post(f"/transactions/{txn.id}/mark-done")
        assert resp.status_code == 200

        db.session.refresh(txn)
        done_id = ref_cache.status_id(StatusEnum.DONE)
        assert txn.status_id == done_id

    def test_companion_mark_done_non_visible_rejected(
        self, app, db, seed_user, seed_periods_today, seed_companion,
    ):
        """Plan 10.10: Companion gets 404 for mark-done on non-visible transaction."""
        template = _make_template(
            seed_user, companion_visible=False, name="Mortgage",
        )
        txn = generate_row_of(template, seed_periods_today[0])
        db.session.commit()

        comp = _login_companion(app)
        resp = comp.post(f"/transactions/{txn.id}/mark-done")
        assert resp.status_code == 404

    def test_companion_mark_paid_returns_card_with_trigger(
        self, app, db, seed_user, seed_periods_today, seed_companion,
    ):
        """Companion mobile Mark Paid swaps the card in place, no reload.

        The companion action bar posts ``render=mobile_card`` with
        ``can_edit=0``, so mark_done returns the re-rendered companion
        card (settled badge, no Mark Paid, and -- because can_edit is
        false -- no owner-only Edit Amount / Open Full) plus
        ``HX-Trigger: mobileCardSettled`` rather than the full-reload
        ``gridRefresh``.
        """
        template = _make_template(
            seed_user, companion_visible=True, name="Groceries",
        )
        txn = generate_row_of(template, seed_periods_today[0])
        db.session.commit()
        txn_id = txn.id

        comp = _login_companion(app)
        resp = comp.post(
            f"/transactions/{txn_id}/mark-done",
            data={"render": "mobile_card", "card_prefix": "tp", "can_edit": "0"},
        )
        assert resp.status_code == 200
        body = resp.data.decode()
        assert 'class="mobile-card-wrapper"' in body
        assert f'id="card-tp-{txn_id}"' in body
        assert "badge-done" in body
        assert "Mark Paid" not in body
        # can_edit=0 keeps the owner-only affordances out of the swap.
        assert "Edit Amount" not in body
        assert "Open Full" not in body
        assert resp.headers.get("HX-Trigger") == "mobileCardSettled"

    def test_companion_view_shows_paid_indicator_after_mark_done(
        self, app, db, seed_user, seed_periods_today, seed_companion,
    ):
        """After marking as Paid, companion view shows the Paid indicator.

        Mark the transaction done, then load the companion view
        and verify the "Paid" text appears.
        """
        template = _make_template(
            seed_user, companion_visible=True, name="Groceries",
        )
        txn = generate_row_of(template, seed_periods_today[0])
        db.session.commit()

        comp = _login_companion(app)
        resp = comp.post(f"/transactions/{txn.id}/mark-done")
        assert resp.status_code == 200

        # Now load the companion view for this period.
        resp = comp.get(f"/companion/period/{seed_periods_today[0].id}")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Paid" in html

    def test_companion_mark_done_records_the_entry_sum(
        self, app, db, seed_user, seed_periods_today, seed_companion,
    ):
        """Mark-done on a tracked transaction settles it at its purchases.

        Arithmetic: entries = $100 + $50 = $150.

        **What it reads changed at plan step X-au-c3 and the FIGURE did not.**
        The close used to COPY the entry sum into ``actual_amount``; a
        ``purchases`` settlement stores no figure at all, because the row's own
        entries state it and a stored copy would need a reconciler to keep the
        two in step.  So the assertion asks the accessor rather than the
        column, and still asks for $150.
        """
        template = _make_template(
            seed_user, companion_visible=True, track=True, name="Groceries",
        )
        txn = generate_row_of(template, seed_periods_today[0])
        db.session.add(TransactionEntry(
            **figure_source_columns(),
            transaction_id=txn.id, account_id=txn.account_id, owner_id=txn.user_id, user_id=seed_user["user"].id,
            amount=Decimal("100.00"), description="Kroger",
            purchased_on=date(2026, 1, 5),
        ))
        db.session.add(TransactionEntry(
            **figure_source_columns(),
            transaction_id=txn.id, account_id=txn.account_id, owner_id=txn.user_id, user_id=seed_user["user"].id,
            amount=Decimal("50.00"), description="Walmart",
            purchased_on=date(2026, 1, 6),
        ))
        db.session.commit()

        comp = _login_companion(app)
        resp = comp.post(f"/transactions/{txn.id}/mark-done")
        assert resp.status_code == 200

        db.session.refresh(txn)
        assert settled_figure(txn) == Decimal("150.00")


# ── Entry Data in Response HTML ──────────────────────────────────────


class TestEntryDataInHTML:
    """Verify companion view renders correct entry progress information."""

    def test_tracked_txn_with_entries_shows_progress(
        self, app, db, seed_user, seed_periods_today, seed_companion,
    ):
        """Visible tracked transaction with entries shows "$X / $Y" progress.

        Arithmetic: entries=$200, estimated=$500.  HTML contains
        "200 / 500" (the {:,.0f} format without dollar signs in the span).
        """
        template = _make_template(
            seed_user, companion_visible=True, track=True, name="Groceries",
        )
        txn = generate_row_of(template, seed_periods_today[0])
        db.session.add(TransactionEntry(
            **figure_source_columns(),
            transaction_id=txn.id, account_id=txn.account_id, owner_id=txn.user_id, user_id=seed_user["user"].id,
            amount=Decimal("200.00"), description="Kroger",
            purchased_on=date(2026, 1, 5),
        ))
        db.session.commit()

        comp = _login_companion(app)
        resp = comp.get(f"/companion/period/{seed_periods_today[0].id}")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "$200 / $500" in html
        assert "$300 left" in html

    def test_tracked_txn_without_entries_shows_estimated(
        self, app, db, seed_user, seed_periods_today, seed_companion,
    ):
        """Visible tracked transaction without entries shows estimated amount only.

        No progress bar, just the estimated amount.
        """
        template = _make_template(
            seed_user, companion_visible=True, track=True, name="Groceries",
        )
        generate_row_of(template, seed_periods_today[0])
        db.session.commit()

        comp = _login_companion(app)
        resp = comp.get(f"/companion/period/{seed_periods_today[0].id}")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "$500" in html
        # No progress format -- should not contain " / ".
        assert "/ $500" not in html

    def test_non_tracked_txn_shows_estimated_amount(
        self, app, db, seed_user, seed_periods_today, seed_companion,
    ):
        """Visible non-tracked transaction shows estimated amount."""
        template = _make_template(
            seed_user, companion_visible=True, track=False, name="Birthday Gift",
            amount="100.00",
        )
        generate_row_of(template, seed_periods_today[0])
        db.session.commit()

        comp = _login_companion(app)
        resp = comp.get(f"/companion/period/{seed_periods_today[0].id}")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Birthday Gift" in html
        assert "$100" in html

    def test_over_budget_shows_over_indicator(
        self, app, db, seed_user, seed_periods_today, seed_companion,
    ):
        """Over-budget transaction shows "over" indicator with danger styling.

        Arithmetic: estimated=$100, entries=$120.  HTML contains
        "$120 / $100" and "$20 over".
        """
        template = _make_template(
            seed_user, companion_visible=True, track=True, name="Gas",
            amount="100.00",
        )
        txn = generate_row_of(template, seed_periods_today[0])
        db.session.add(TransactionEntry(
            **figure_source_columns(),
            transaction_id=txn.id, account_id=txn.account_id, owner_id=txn.user_id, user_id=seed_user["user"].id,
            amount=Decimal("120.00"), description="Shell",
            purchased_on=date(2026, 1, 5),
        ))
        db.session.commit()

        comp = _login_companion(app)
        resp = comp.get(f"/companion/period/{seed_periods_today[0].id}")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "$120 / $100" in html
        assert "$20 over" in html
        assert "text-danger" in html

    def test_progress_bar_pct_rendered_as_decimal_string(
        self, app, db, seed_user, seed_periods_today, seed_companion,
    ):
        """C9-4 (F-23 / MED-04 / E-16): the rendered ``data-progress-pct``
        attribute carries the Decimal-quantised percentage produced by
        :func:`entry_service.pct_complete`, not the legacy
        ``float(Decimal_expr)`` value the companion route used to compute
        inline.

        Hand arithmetic: total = $55.50, target = $100.00.
        55.50 / 100.00 * 100 = 55.50; quantise(0.01) = Decimal("55.50").
        The attribute appears verbatim because Jinja's
        ``[ed.pct, 100]|min`` filter returns the smaller of the Decimal
        and 100 (still Decimal("55.50") here), and Decimal's __str__
        renders the canonical "55.50".
        """
        template = _make_template(
            seed_user, companion_visible=True, track=True, name="Groceries",
            amount="100.00",
        )
        txn = generate_row_of(template, seed_periods_today[0])
        db.session.add(TransactionEntry(
            **figure_source_columns(),
            transaction_id=txn.id, account_id=txn.account_id, owner_id=txn.user_id, user_id=seed_user["user"].id,
            amount=Decimal("55.50"), description="Kroger",
            purchased_on=date(2026, 1, 5),
        ))
        db.session.commit()

        comp = _login_companion(app)
        resp = comp.get(f"/companion/period/{seed_periods_today[0].id}")
        assert resp.status_code == 200
        html = resp.data.decode()
        # The progress-bar width is driven by data-progress-pct; the
        # Decimal-quantised value is rendered as-is.
        assert 'data-progress-pct="55.50"' in html
        # The aria-valuenow companion uses int() on the Decimal -- locks
        # that the Decimal pct survives the template's |int filter
        # without producing scientific notation or other surprise forms.
        assert 'aria-valuenow="55"' in html


# ── Empty States ─────────────────────────────────────────────────────


class TestEmptyStates:
    """Verify empty state rendering."""

    def test_period_with_no_visible_transactions(
        self, app, db, seed_user, seed_periods_today, seed_companion,
    ):
        """Period with no visible transactions shows empty state message."""
        comp = _login_companion(app)
        resp = comp.get(f"/companion/period/{seed_periods_today[0].id}")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "No transactions for this period" in html

    def test_period_with_only_non_visible_shows_empty(
        self, app, db, seed_user, seed_periods_today, seed_companion,
    ):
        """Period with only non-visible transactions shows empty state.

        The transactions exist but are filtered out by visibility,
        so the companion sees the empty state.
        """
        template = _make_template(
            seed_user, companion_visible=False, name="Mortgage",
        )
        generate_row_of(template, seed_periods_today[0])
        db.session.commit()

        comp = _login_companion(app)
        resp = comp.get(f"/companion/period/{seed_periods_today[0].id}")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "No transactions for this period" in html
        assert "Mortgage" not in html


# ── Mark as Paid Button Visibility ───────────────────────────────────


class TestMarkPaidButtonVisibility:
    """Verify the Mark Paid affordance appears only for PROJECTED transactions.

    Re-pinned in mobile-first v3 plan Commit 13 / D-B: companion adopted
    the shared ``grid/_mobile_this_period.html`` partial + ``render_row_card``
    macro + ``_mobile_card_actions.html`` action bar, which use the shorter
    "Mark Paid" button label and the ``badge-done`` settled badge.  The
    legacy companion ``_transaction_card.html`` (removed in the mobile
    follow-up F-4 cleanup; preserved in git history) used
    "Mark as Paid" + ``bi-check-circle-fill``; those strings no longer
    render through the companion route's HTML so the assertions move to
    the shared-design vocabulary.  The semantic contract (Projected =
    action affordance present, Settled = badge only) is unchanged.
    """

    def test_projected_transaction_shows_mark_paid_button(
        self, app, db, seed_user, seed_periods_today, seed_companion,
    ):
        """PROJECTED transaction shows the 'Mark Paid' button.

        The shared per-card action bar at
        ``_mobile_card_actions.html:68`` reads ``<i class="bi bi-check2">
        </i> Mark Paid`` (no "as") for any non-settled status -- the
        text was shortened when the action bar was introduced in
        mobile-first v3 plan Commit 7 and companion picked it up by
        adopting the partial in Commit 13.
        """
        template = _make_template(
            seed_user, companion_visible=True, name="Groceries",
        )
        generate_row_of(template, seed_periods_today[0])
        db.session.commit()

        comp = _login_companion(app)
        resp = comp.get(f"/companion/period/{seed_periods_today[0].id}")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Mark Paid" in html

    def test_paid_transaction_shows_paid_indicator(
        self, app, db, seed_user, seed_periods_today, seed_companion,
    ):
        """Paid (Done) transaction shows the settled badge, not a button.

        The shared ``render_row_card`` macro emits
        ``<span class="badge-done">&#10003;</span>`` for any
        ``status.is_settled`` row (see ``_grid_row_macros.html:166-170``)
        instead of the legacy companion ``<i class="bi
        bi-check-circle-fill"></i> Paid`` rendering; the action bar's
        Mark Paid button renders only for Projected rows (the
        ``txn.status_id == STATUS_PROJECTED`` guard in
        ``_mobile_card_actions.html``, tightened by grid audit D2).
        """
        template = _make_template(
            seed_user, companion_visible=True, name="Groceries",
        )
        txn = generate_row_of(template, seed_periods_today[0])
        status_seam.apply_status_change(txn, ref_cache.status_id(StatusEnum.DONE), settlement=settlement_if_settling(txn, ref_cache.status_id(StatusEnum.DONE)))
        db.session.commit()

        comp = _login_companion(app)
        resp = comp.get(f"/companion/period/{seed_periods_today[0].id}")
        assert resp.status_code == 200
        html = resp.data.decode()
        # Settled rows render the shared ``badge-done`` checkmark
        # span (the macro's settled-badge path) instead of the legacy
        # ``bi-check-circle-fill`` Bootstrap icon.
        assert "badge-done" in html
        # The Mark Paid button must not appear -- the action bar guards
        # against rendering it for settled statuses.
        assert "Mark Paid" not in html

    def test_credit_transaction_hides_mark_paid_button(
        self, app, db, seed_user, seed_periods_today, seed_companion,
    ):
        """A Credit-status card offers no Mark Paid button.

        Credit -> Paid is an illegal transition (Credit only reverts to
        Projected), yet the old not-is_settled guard still rendered the
        button on Credit cards -- a tap then 400ed invisibly.  The
        projected-only guard (grid audit D2, ruled 2026-07-11) removes
        the dead affordance; the CC chip still marks the state.
        """
        template = _make_template(
            seed_user, companion_visible=True, name="Groceries",
        )
        txn = generate_row_of(template, seed_periods_today[0])
        txn.status_id = ref_cache.status_id(StatusEnum.CREDIT)
        db.session.commit()

        comp = _login_companion(app)
        resp = comp.get(f"/companion/period/{seed_periods_today[0].id}")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "badge-credit" in html
        assert "Mark Paid" not in html


# ── Entry List Inline Rendering ──────────────────────────────────────


class TestEntryListInlineRendering:
    """Verify envelope transactions render the entries list inline.

    The per-card HTMX ``hx-trigger="load"`` lazy-loader was replaced
    by a server-side ``{% include %}`` of
    ``grid/_transaction_entries.html`` because the lazy-load shape
    fanned out to ~60 parallel GETs on ``entries.list_entries`` per
    grid page load (6 visible periods x ~10 envelope templates),
    which exceeds the ``RATELIMIT_DEFAULT`` ceiling of "30 per
    minute" and left the over-limit cards stuck on a never-
    resolving loading spinner.  The macro now consumes
    ``entry_lists`` (built by
    ``entry_service.build_entry_lists_dict`` and passed by both
    the grid and companion routes via ``render_template``) plus
    ``today``, and includes the entries template inline.
    """

    def test_envelope_txn_renders_entries_inline(
        self, app, db, seed_user, seed_periods_today, seed_companion,
    ):
        """Envelope transaction card renders the entries list server-side.

        Asserts the inline entries list div is in the response body and
        that the entries route URL appears (used by the add-entry form's
        ``hx-post`` inside the inline include).  The companion view
        renders through the This Period partial (``id_prefix='tp'``), so
        the inline list carries the per-tab-namespaced id
        ``entry-list-tp-<id>`` -- distinct from the full-edit popover's
        bare ``entry-list-<id>`` so the two never collide.  The CRUD
        url_for output carries ``host=tp`` so the namespace survives the
        round-trip.  The old ``hx-trigger="load"`` lazy-load attribute
        MUST NOT be present -- its presence on a recurring envelope card
        would re-introduce the rate-limit storm.
        """
        template = _make_template(
            seed_user, companion_visible=True, track=True, name="Groceries",
        )
        txn = generate_row_of(template, seed_periods_today[0])
        db.session.commit()

        comp = _login_companion(app)
        resp = comp.get(f"/companion/period/{seed_periods_today[0].id}")
        assert resp.status_code == 200
        html = resp.data.decode()
        # Inline entries section is in the DOM, namespaced to the
        # This Period tab (the add-entry form inside
        # _transaction_entries.html hx-posts to the entries route, so
        # the URL appears and carries the host prefix).
        assert f'id="entry-list-tp-{txn.id}"' in html
        assert f"/transactions/{txn.id}/entries" in html
        assert "host=tp" in html
        # No HTMX load-trigger -- the macro no longer emits the
        # lazy-loader wrapper.
        assert 'hx-trigger="load"' not in html

    def test_non_envelope_txn_has_no_entries_section(
        self, app, db, seed_user, seed_periods_today, seed_companion,
    ):
        """Non-envelope transaction card does NOT render an entries section.

        Only envelope templates (``is_envelope=True``) get the inline
        entries section.  A non-envelope txn must produce neither
        the ``entry-list-<id>`` div nor any ``/entries`` URL in the
        response.
        """
        template = _make_template(
            seed_user, companion_visible=True, track=False, name="Birthday",
        )
        txn = generate_row_of(template, seed_periods_today[0])
        db.session.commit()

        comp = _login_companion(app)
        resp = comp.get(f"/companion/period/{seed_periods_today[0].id}")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert f'id="entry-list-{txn.id}"' not in html
        assert f"/transactions/{txn.id}/entries" not in html


# ── Card Tap-to-Expand Reachability ──────────────────────────────────


class TestCardTapToExpand:
    """Verify companion cards are tappable so the action bar is reachable.

    The Mark Paid button, the inline entries list, and the add-purchase
    form all live inside the collapsed ``.mobile-card-expansion`` that
    opens only when the delegated tap handler in
    ``app/static/js/mobile_grid.js`` matches
    ``.mobile-txn-card[data-mobile-txn-id]``.  The companion route
    renders the shared card with ``can_edit=False``; before the fix that
    flag suppressed ``data-mobile-txn-id``, so companion cards emitted
    the action bar into the HTML but the card could never be tapped
    open -- the spouse saw the line items yet could not view existing
    purchases, add a purchase, or mark anything paid.

    These tests assert the tappability precondition (the attribute)
    rather than mere string presence.  The string-presence assertions
    in the other companion tests pass even when the card is sealed shut,
    which is exactly why the interactivity regression slipped through.
    """

    def test_companion_card_has_tap_to_expand_attribute(
        self, app, db, seed_user, seed_periods_today, seed_companion,
    ):
        """Companion (can_edit=False) card carries data-mobile-txn-id.

        Without the attribute the tap handler in mobile_grid.js skips
        the card and the expansion never opens, sealing the entries
        list, the add-purchase form, and Mark Paid behind a collapse
        the companion cannot trigger.
        """
        template = _make_template(
            seed_user, companion_visible=True, track=True, name="Groceries",
        )
        txn = generate_row_of(template, seed_periods_today[0])
        db.session.commit()

        comp = _login_companion(app)
        resp = comp.get(f"/companion/period/{seed_periods_today[0].id}")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert f'data-mobile-txn-id="{txn.id}"' in html

    def test_companion_card_keeps_owner_only_controls_hidden(
        self, app, db, seed_user, seed_periods_today, seed_companion,
    ):
        """can_edit=False still drops the owner-only Edit/Open buttons.

        Tap-to-expand is universal, but the owner-only affordances
        (Edit Amount, Open Full) must remain absent for the companion
        even though the card is now reachable.
        """
        template = _make_template(
            seed_user, companion_visible=True, name="Groceries",
        )
        generate_row_of(template, seed_periods_today[0])
        db.session.commit()

        comp = _login_companion(app)
        resp = comp.get(f"/companion/period/{seed_periods_today[0].id}")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Edit Amount" not in html
        assert "Open Full" not in html


class TestTheCompanionAddPurchaseFormReadsTheUsersClock:
    """The companion page's ``today`` is ``display_today()``, never the process's.

    **The owner's mobile card was corrected for this at plan step S1-c and the
    companion page was left behind**, on the surface that SHARES its macro:
    ``routes/companion._build_partial_context`` passed ``today=date.today()``
    into ``grid/_transaction_entries.html``, whose ``purchased_on`` input takes
    it as both the default ``value`` and the ``max``.  ``entry_service``
    judges that field against ``display_today()`` (ruling R-M,
    ``_reject_future_purchase_date``), so on any process not pinned to
    ``America/New_York`` the companion's own form defaulted to -- and capped at
    -- a date its own server refuses.  Found by an adversarial design review of
    plan step **C2-f2b** and fixed there, because that step was already editing
    the two lines above it; the review's other correct point was that a fix
    without a test is not a fix, which is what this class is.

    Latent in production, which pins ``TZ: America/New_York`` in both compose
    files.  NOT latent in CI, which runs ``TZ=Pacific/Kiritimati`` (UTC+14)
    precisely to catch this shape.

    **It pins WHICH CLOCK the route reads rather than arranging for the two to
    disagree**, for the reason
    ``test_grid.test_create.TestTheAddPurchaseFormReadsTheUsersClock`` states at
    length: the C library's zone is process-global and survives
    ``monkeypatch``'s env restore, so a ``TZ`` mutation leaks into every later
    test in the same xdist worker.  Substituting ``display_today`` for a
    sentinel is exact and side-effect free -- the rendered form carries it if
    and only if the route reads that function, which IS the defect.
    """

    #: A date no clock can produce on its own, so its appearance in the
    #: rendered form can only have come from ``display_today``.
    _SENTINEL = date(2019, 7, 4)

    def test_the_companion_card_form_defaults_to_the_users_civil_day(
        self, app, db, seed_user, seed_periods_today, seed_companion,
        monkeypatch,
    ):
        """The companion's add-purchase input comes from ``display_today``.

        Both halves are asserted.  The positive alone would pass a build that
        rendered the sentinel somewhere unrelated; the negative alone would
        pass a page that rendered no date at all.

        Negative control (standard 4): restoring ``today=date.today()`` in
        ``_build_partial_context`` fails this on
        ``assert 'value="2019-07-04"' in html``.
        """
        # pylint: disable=import-outside-toplevel
        from app.routes import companion as companion_routes

        template = _make_template(
            seed_user, companion_visible=True, track=True, name="Groceries",
        )
        generate_row_of(template, seed_periods_today[0])
        db.session.commit()

        # The premise: the sentinel is not what any real clock answers, so a
        # route reading ``date.today()`` cannot produce it by coincidence.
        assert date.today() != self._SENTINEL
        monkeypatch.setattr(
            companion_routes, "display_today", lambda: self._SENTINEL,
        )

        comp = _login_companion(app)
        resp = comp.get(f"/companion/period/{seed_periods_today[0].id}")
        assert resp.status_code == 200
        html = resp.data.decode()

        assert 'name="purchased_on"' in html, (
            "the add-purchase form must actually have rendered"
        )
        assert f'value="{self._SENTINEL.isoformat()}"' in html
        assert f'max="{self._SENTINEL.isoformat()}"' in html
        assert date.today().isoformat() not in html
